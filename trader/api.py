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


@app.get("/api/test_trades")
async def test_trades_endpoint():
    """Test endpoint to verify what symbols have trades"""
    import asyncio
    from datetime import datetime, timezone

    client = await get_client()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - 90 * 86_400_000  # 90 days

    result = {"symbols_tested": [], "total_trades": 0}

    # Test each configured symbol (no time filter to get all trades)
    for sym in SYMBOL_CONFIGS.keys():
        try:
            resp = await asyncio.to_thread(
                lambda s=sym: client.rest_api.account_trade_list(
                    symbol=s, limit=500
                )
            )
            trades_data = resp.data()
            count = len(trades_data)
            sample = None
            if count > 0:
                # Get first trade details
                t = trades_data[0]
                sample = {
                    "time": int(t.time),
                    "date": datetime.fromtimestamp(int(t.time)/1000, tz=timezone.utc).isoformat(),
                    "side": t.side,
                    "price": _safe_float(t.price),
                    "qty": _safe_float(t.qty),
                    "pnl": _safe_float(t.realized_pnl),
                }
            result["symbols_tested"].append({"symbol": sym, "count": count, "sample": sample})
            result["total_trades"] += count
        except Exception as e:
            result["symbols_tested"].append({"symbol": sym, "error": str(e)})

    return result

@app.get("/api/trades")
async def get_trades(symbol: str | None = None, days: int = 7):
    client = await get_client()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_ms = now_ms - days * 86_400_000

    symbols = [symbol.upper()] if symbol else list(SYMBOL_CONFIGS.keys())

    all_trades: list[dict] = []
    for sym in symbols:
        try:
            # Fetch all trades (no time filter) since Binance API limits to 7 days max
            resp = await asyncio.to_thread(
                lambda s=sym: client.rest_api.account_trade_list(
                    symbol=s, limit=500
                )
            )
            trades = resp.data()
            for t in trades:
                trade_time = int(t.time)
                # Filter by time in Python instead
                if trade_time < cutoff_ms:
                    continue
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
            pass  # Skip symbols with errors

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


@app.get("/api/account_summary")
async def get_account_summary():
    """
    Comprehensive account metrics combining:
    - Balance (total, available, in positions)
    - All open positions with unrealized P&L
    - Total equity (balance + unrealized P&L)
    - 24h change metrics
    """
    try:
        client = await get_client()

        # Get balance
        balance_resp = await asyncio.to_thread(client.rest_api.futures_account_balance_v3)
        usdt_balance = next(
            (b for b in balance_resp.data() if b.asset == "USDT"), None
        )
        total_balance = _safe_float(usdt_balance.balance) if usdt_balance else 0
        available_balance = _safe_float(usdt_balance.available_balance) if usdt_balance else 0

        # Get positions
        pos_resp = await asyncio.to_thread(client.rest_api.position_information_v3)
        total_unrealized_pnl = 0
        total_position_margin = 0
        open_positions = 0

        for p in pos_resp.data():
            pos_amt = abs(_safe_float(p.position_amt))
            if pos_amt > 0:
                open_positions += 1
                total_unrealized_pnl += _safe_float(p.un_realized_profit)
                entry_price = _safe_float(p.entry_price)
                leverage = int(_safe_float(getattr(p, "leverage", 1)))
                total_position_margin += (pos_amt * entry_price) / leverage if leverage else 0

        # Calculate total equity
        total_equity = total_balance + total_unrealized_pnl

        # Get 24h account metrics (from income history)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        day_ago_ms = now_ms - 86_400_000

        try:
            income_resp = await asyncio.to_thread(
                lambda: client.rest_api.income_history(
                    start_time=day_ago_ms,
                    limit=1000
                )
            )
            pnl_24h = sum(
                _safe_float(i.income)
                for i in income_resp.data()
                if i.income_type in ("REALIZED_PNL", "FUNDING_FEE")
            )
        except Exception:
            pnl_24h = 0

        return {
            "total_balance": round(total_balance, 2),
            "available_balance": round(available_balance, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "position_margin": round(total_position_margin, 2),
            "open_positions": open_positions,
            "pnl_24h": round(pnl_24h, 2),
            "equity_change_24h_pct": round((pnl_24h / (total_equity - pnl_24h) * 100), 2) if (total_equity - pnl_24h) > 0 else 0,
        }
    except Exception as e:
        return {
            "error": str(e),
            "total_balance": 0,
            "available_balance": 0,
            "total_equity": 0,
            "unrealized_pnl": 0,
            "position_margin": 0,
            "open_positions": 0,
            "pnl_24h": 0,
            "equity_change_24h_pct": 0,
        }


@app.get("/api/market_data")
async def get_market_data():
    """
    Get 24h market statistics for all trading symbols.
    Includes: price change %, volume, high/low, last price
    """
    try:
        # Get 24h ticker data for all symbols
        url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        raw = await asyncio.to_thread(
            lambda: urllib.request.urlopen(url, timeout=10).read()
        )
        all_tickers = json.loads(raw)

        # Filter to only symbols we're trading
        trading_symbols = set(cfg.symbol for cfg in SYMBOL_CONFIGS.values())

        market_data = {}
        for ticker in all_tickers:
            symbol = ticker["symbol"]
            if symbol in trading_symbols:
                market_data[symbol] = {
                    "symbol": symbol,
                    "last_price": float(ticker["lastPrice"]),
                    "price_change_pct": float(ticker["priceChangePercent"]),
                    "high_24h": float(ticker["highPrice"]),
                    "low_24h": float(ticker["lowPrice"]),
                    "volume_24h": float(ticker["volume"]),
                    "quote_volume_24h": float(ticker["quoteVolume"]),
                    "trades_24h": int(ticker["count"]),
                }

        return {"market_data": market_data}
    except Exception as e:
        return {"error": str(e), "market_data": {}}


@app.get("/api/performance")
async def get_performance():
    """
    Bot performance metrics calculated from bot states and trade history.
    Shows per-bot statistics and overall portfolio performance.
    """
    bot_states = bot_registry.get_states()
    trades_data = await get_trades(days=30)

    # Calculate per-bot metrics from trades
    bot_metrics = {}
    for bot_key, bot_state in bot_states.items():
        symbol = bot_state.get("symbol", "")
        strategy = bot_state.get("strategy", "")

        # Filter trades for this symbol
        symbol_trades = [t for t in trades_data["trades"] if t["symbol"] == symbol]

        total_trades = len(symbol_trades)
        winning_trades = sum(1 for t in symbol_trades if t["realized_pnl"] > 0)
        total_pnl = sum(t["realized_pnl"] for t in symbol_trades)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        bot_metrics[bot_key] = {
            "symbol": symbol,
            "strategy": strategy,
            "state": bot_state.get("state", "UNKNOWN"),
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "unrealized_pnl": round(bot_state.get("unrealized_pnl", 0), 2),
        }

    # Overall portfolio stats
    total_trades_all = len(trades_data["trades"])
    winning_trades_all = sum(1 for t in trades_data["trades"] if t["realized_pnl"] > 0)
    total_pnl_all = sum(t["realized_pnl"] for t in trades_data["trades"])

    return {
        "bots": bot_metrics,
        "portfolio": {
            "total_trades": total_trades_all,
            "winning_trades": winning_trades_all,
            "win_rate": round((winning_trades_all / total_trades_all * 100), 1) if total_trades_all > 0 else 0,
            "total_pnl": round(total_pnl_all, 2),
        }
    }


# ── WebSocket feed ─────────────────────────────────────────────────────────────

@app.websocket("/ws/feed")
async def ws_feed(websocket: WebSocket):
    await websocket.accept()
    q = await events.subscribe()
    try:
        while True:
            event = await q.get()
            await websocket.send_json(event)
    except (WebSocketDisconnect, Exception):
        events.unsubscribe(q)


@app.websocket("/ws/logs/{bot_key}")
async def ws_logs(websocket: WebSocket, bot_key: str):
    """Stream real-time logs for a specific bot (e.g., 'BTCUSDT:momshort')."""
    await websocket.accept()

    # Get Redis client
    import os
    import redis.asyncio as aioredis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_client = await aioredis.from_url(redis_url, decode_responses=True)

    try:
        # Send recent log history first
        from trader.log_publisher import get_log_history
        history = await get_log_history(redis_client, bot_key, limit=100)
        for log_entry in reversed(history):  # Send oldest first
            await websocket.send_json(log_entry)

        # Subscribe to real-time logs
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"logs:{bot_key}")

        async for message in pubsub.listen():
            if message["type"] == "message":
                log_entry = json.loads(message["data"])
                await websocket.send_json(log_entry)

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        await pubsub.unsubscribe(f"logs:{bot_key}")
        await redis_client.close()


# ── Serve built frontend (production) ─────────────────────────────────────────

_dist = Path(__file__).parent.parent / "frontend" / "dist"

if _dist.exists():
    _assets = _dist / "assets"
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        # Don't serve SPA for API or WebSocket paths
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not found")
        index = _dist / "index.html"
        return FileResponse(str(index))
