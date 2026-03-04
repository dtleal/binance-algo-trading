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
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from trader.config import BINANCE_API_KEY, BINANCE_SECRET_KEY, SOCKS_PROXY, SYMBOL_CONFIGS
from trader import events, bot_registry

_EQUITY_STREAM = "equity:history"
_EQUITY_MAXLEN = 2016   # 7 days × 24h × 12 snapshots/h (5 min interval)



async def _equity_snapshot_loop() -> None:
    """Background task: snapshot account equity every 5 minutes into Redis and DB."""
    import os
    import redis.asyncio as aioredis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    r = await aioredis.from_url(redis_url, decode_responses=True)

    while True:
        try:
            summary = await get_account_summary()
            now = datetime.now(timezone.utc)
            ts = int(now.timestamp() * 1000)

            # Write to Redis stream (short-term, 7-day window)
            await r.xadd(
                _EQUITY_STREAM,
                {
                    "time":           str(ts),
                    "equity":         str(summary["total_equity"]),
                    "unrealized_pnl": str(summary["unrealized_pnl"]),
                    "balance":        str(summary["total_balance"]),
                },
                maxlen=_EQUITY_MAXLEN,
                approximate=True,
            )

            # Write to DB (persistent, long-term)
            try:
                import db
                pool = db.get_pool()
                await pool.execute(
                    """
                    INSERT INTO equity_snapshots (snapshot_time, total_equity, unrealized_pnl, total_balance)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (snapshot_time) DO NOTHING
                    """,
                    now,
                    summary["total_equity"],
                    summary["unrealized_pnl"],
                    summary["total_balance"],
                )
            except Exception:
                pass  # DB write failure is non-fatal

        except Exception:
            pass
        await asyncio.sleep(300)


app = FastAPI(title="Binance Trader Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _run_trade_uds(pool, client) -> None:
    """User Data Stream in the API server: real-time trade inserts on every order fill."""
    try:
        from binance_sdk_derivatives_trading_usds_futures import DerivativesTradingUsdsFutures
        from binance_common.configuration import ConfigurationWebSocketStreams
        from binance_sdk_derivatives_trading_usds_futures.websocket_streams.client import (
            DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
        )
        from trader.user_data_stream import UserDataStream
        from db.sync_trades import handle_order_trade_update

        uds = UserDataStream(
            client,
            DerivativesTradingUsdsFutures,
            DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
            ConfigurationWebSocketStreams,
        )
        uds.register(lambda e: handle_order_trade_update(pool, e))
        await uds.run()
    except Exception as e:
        import logging
        logging.getLogger("trader.api").warning("Trade UDS failed: %s", e)


@app.on_event("startup")
async def _startup():
    import db
    import db.migrate as db_migrate
    import db.sync_trades as db_sync

    try:
        await db.init_pool()
        await db_migrate.run()
        client = await get_client()
        pool = db.get_pool()
        asyncio.create_task(db_sync.run_sync_loop(pool, client))
        asyncio.create_task(_run_trade_uds(pool, client))
    except Exception as e:
        # DB unavailable — log and continue (Binance fallback still works)
        import logging
        logging.getLogger("trader.api").warning("DB init failed: %s — falling back to Binance API", e)

    asyncio.create_task(_equity_snapshot_loop())


@app.on_event("shutdown")
async def _shutdown():
    import db
    await db.close_pool()

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


# ── Position action helpers ───────────────────────────────────────────────────

async def _do_close_position(client, symbol: str) -> dict:
    """Cancel all orders and market-close a position."""
    sym = symbol.upper()
    try:
        await asyncio.to_thread(client.rest_api.cancel_all_open_orders, symbol=sym)
    except Exception:
        pass
    try:
        await asyncio.to_thread(client.rest_api.cancel_all_algo_open_orders, symbol=sym)
    except Exception:
        pass
    resp = await asyncio.to_thread(client.rest_api.position_information_v3, symbol=sym)
    pos = next((p for p in resp.data() if abs(_safe_float(p.position_amt)) > 0), None)
    if not pos:
        return {"ok": False, "error": "Sem posição aberta"}
    qty = abs(_safe_float(pos.position_amt))
    direction = "long" if _safe_float(pos.position_amt) > 0 else "short"
    close_side = "SELL" if direction == "long" else "BUY"
    order = await asyncio.to_thread(
        client.rest_api.new_order,
        symbol=sym, side=close_side, type="MARKET",
        quantity=str(qty), reduce_only="true", new_order_resp_type="RESULT",
    )
    avg = _safe_float(order.data().avg_price)
    entry = _safe_float(pos.entry_price)
    pnl = (avg - entry) * qty if direction == "long" else (entry - avg) * qty
    return {"ok": True, "symbol": sym, "direction": direction, "close_price": avg, "pnl": round(pnl, 4), "qty": qty}


@app.post("/api/positions/close_all")
async def api_close_all_positions():
    """Close all open positions immediately."""
    client = await get_client()
    data = await get_positions()
    results = []
    for pos in data["positions"]:
        result = await _do_close_position(client, pos["symbol"])
        results.append(result)
    return {"ok": True, "results": results}


@app.post("/api/positions/{symbol}/close")
async def api_close_position(symbol: str):
    """Cancel all orders and market-close one position."""
    client = await get_client()
    return await _do_close_position(client, symbol)


@app.post("/api/positions/{symbol}/breakeven")
async def api_breakeven(symbol: str):
    """Move SL to entry price (breakeven). Cancels existing algo orders and re-places SL+TP."""
    import redis.asyncio as aioredis
    sym = symbol.upper()
    client = await get_client()

    # Get current position
    resp = await asyncio.to_thread(client.rest_api.position_information_v3, symbol=sym)
    pos = next((p for p in resp.data() if abs(_safe_float(p.position_amt)) > 0), None)
    if not pos:
        return {"ok": False, "error": "Sem posição aberta"}

    entry_price = _safe_float(pos.entry_price)
    qty = abs(_safe_float(pos.position_amt))
    direction = "long" if _safe_float(pos.position_amt) > 0 else "short"
    close_side = "SELL" if direction == "long" else "BUY"

    # Try to get tp_price from Redis bot state
    tp_price = None
    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        r = await aioredis.from_url(redis_url, decode_responses=True)
        states = await r.hgetall("bot:states")
        for key, raw in states.items():
            if key.startswith(sym + ":"):
                state = json.loads(raw)
                tp_price = state.get("tp_price")
                break
    except Exception:
        pass

    # Cancel all existing algo orders
    try:
        await asyncio.to_thread(client.rest_api.cancel_all_algo_open_orders, symbol=sym)
    except Exception:
        pass

    # Place new SL at entry price
    await asyncio.to_thread(
        client.rest_api.new_algo_order,
        algo_type="CONDITIONAL", symbol=sym, side=close_side,
        type="STOP_MARKET", trigger_price=str(entry_price), close_position="true",
    )

    # Re-place TP if we have the price
    if tp_price:
        try:
            await asyncio.to_thread(
                client.rest_api.new_algo_order,
                algo_type="CONDITIONAL", symbol=sym, side=close_side,
                type="TAKE_PROFIT_MARKET", trigger_price=str(tp_price), close_position="true",
            )
        except Exception:
            pass

    return {"ok": True, "symbol": sym, "sl_moved_to": entry_price, "tp_price": tp_price}


@app.post("/api/positions/{symbol}/invert")
async def api_invert_position(symbol: str):
    """Close current position and open the opposite direction with same qty + SL/TP from config."""
    sym = symbol.upper()
    client = await get_client()

    # Get current position before closing
    resp = await asyncio.to_thread(client.rest_api.position_information_v3, symbol=sym)
    pos = next((p for p in resp.data() if abs(_safe_float(p.position_amt)) > 0), None)
    if not pos:
        return {"ok": False, "error": "Sem posição aberta"}

    qty = abs(_safe_float(pos.position_amt))
    direction = "long" if _safe_float(pos.position_amt) > 0 else "short"
    new_direction = "short" if direction == "long" else "long"
    new_side = "SELL" if new_direction == "short" else "BUY"
    close_algo_side = "BUY" if new_direction == "short" else "SELL"

    # Close current position
    close_result = await _do_close_position(client, sym)
    if not close_result.get("ok"):
        return close_result
    new_entry = close_result["close_price"]

    # Get SL/TP pcts from config (fallback to defaults)
    cfg = SYMBOL_CONFIGS.get(sym)
    sl_pct = cfg.sl_pct if cfg else 5.0
    tp_pct = cfg.tp_pct if cfg else 10.0

    import math
    if new_direction == "long":
        sl_price = round(new_entry * (1 - sl_pct / 100), 4)
        tp_price = round(new_entry * (1 + tp_pct / 100), 4)
    else:
        sl_price = round(new_entry * (1 + sl_pct / 100), 4)
        tp_price = round(new_entry * (1 - tp_pct / 100), 4)

    # Open new position
    order = await asyncio.to_thread(
        client.rest_api.new_order,
        symbol=sym, side=new_side, type="MARKET",
        quantity=str(qty), new_order_resp_type="RESULT",
    )

    # Place SL + TP
    try:
        await asyncio.to_thread(
            client.rest_api.new_algo_order,
            algo_type="CONDITIONAL", symbol=sym, side=close_algo_side,
            type="STOP_MARKET", trigger_price=str(sl_price), close_position="true",
        )
        await asyncio.to_thread(
            client.rest_api.new_algo_order,
            algo_type="CONDITIONAL", symbol=sym, side=close_algo_side,
            type="TAKE_PROFIT_MARKET", trigger_price=str(tp_price), close_position="true",
        )
    except Exception:
        pass

    return {
        "ok": True, "symbol": sym,
        "closed": direction, "opened": new_direction,
        "qty": qty, "entry": new_entry, "sl": sl_price, "tp": tp_price,
    }


@app.get("/api/test_trades")
async def test_trades_endpoint():
    """Summary of trades per symbol from DB."""
    import db
    pool = db.get_pool()
    rows = await pool.fetch(
        """
        SELECT symbol,
               COUNT(*) AS total_fills,
               SUM(CASE WHEN realized_pnl != 0 THEN 1 ELSE 0 END) AS closing_fills,
               MIN(trade_time) AS earliest,
               MAX(trade_time) AS latest
        FROM trades
        GROUP BY symbol
        ORDER BY symbol
        """
    )
    symbols = [
        {
            "symbol":        r["symbol"],
            "total_fills":   r["total_fills"],
            "closing_fills": r["closing_fills"],
            "earliest":      r["earliest"].isoformat() if r["earliest"] else None,
            "latest":        r["latest"].isoformat() if r["latest"] else None,
        }
        for r in rows
    ]
    return {"symbols": symbols, "total_fills": sum(r["total_fills"] for r in symbols)}

@app.get("/api/strategies")
async def get_strategies():
    """Return all strategies from DB."""
    import db
    pool = db.get_pool()
    rows = await pool.fetch(
        "SELECT name, bot_command, direction, active FROM strategies ORDER BY name"
    )
    return {"strategies": [dict(r) for r in rows]}


@app.get("/api/trades")
async def get_trades(symbol: str | None = None, days: int = 7):
    import db
    from db.queries.trades import get_trades as db_get_trades
    pool = db.get_pool()
    all_trades = await db_get_trades(pool, symbol=symbol, days=days)
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
    import db
    from db.queries.trades import get_commissions as db_get_commissions
    pool = db.get_pool()
    return await db_get_commissions(pool, days=days)


@app.get("/api/bot_states")
async def get_bot_states():
    states = bot_registry.get_states()
    try:
        import db
        pool = db.get_pool()
        rows = await pool.fetch("SELECT symbol, mode FROM symbol_configs")
        mode_map = {r["symbol"]: r["mode"] for r in rows}
        for state in states.values():
            sym = state.get("symbol", "").upper()
            state["mode"] = mode_map.get(sym, "normal")
    except Exception:
        pass
    return {"bots": states}


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

        # Get 24h P&L from DB (closing fills only)
        try:
            import db
            pool = db.get_pool()
            pnl_24h = await pool.fetchval(
                """
                SELECT COALESCE(SUM(realized_pnl), 0)
                FROM trades
                WHERE realized_pnl != 0
                  AND trade_time >= NOW() - INTERVAL '24 hours'
                """
            )
            pnl_24h = float(pnl_24h)
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


@app.get("/api/equity_history")
async def get_equity_history(days: int = 7):
    """Return equity snapshots (5-min interval) — DB-first, Redis fallback."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # DB path (persistent, long-term history)
    try:
        import db
        pool = db.get_pool()
        rows = await pool.fetch(
            """
            SELECT snapshot_time, total_equity, unrealized_pnl, total_balance
            FROM equity_snapshots
            WHERE snapshot_time >= $1
            ORDER BY snapshot_time ASC
            """,
            cutoff,
        )
        if rows:
            return {
                "snapshots": [
                    {
                        "time":           int(r["snapshot_time"].timestamp() * 1000),
                        "equity":         float(r["total_equity"]),
                        "unrealized_pnl": float(r["unrealized_pnl"]),
                        "balance":        float(r["total_balance"]),
                    }
                    for r in rows
                ]
            }
    except Exception:
        pass

    # Redis fallback (recent window only)
    import os
    import redis.asyncio as aioredis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    r = await aioredis.from_url(redis_url, decode_responses=True)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    try:
        messages = await r.xrange(_EQUITY_STREAM, min=f"{cutoff_ms}-0")
        return {
            "snapshots": [
                {
                    "time":           int(data["time"]),
                    "equity":         float(data["equity"]),
                    "unrealized_pnl": float(data["unrealized_pnl"]),
                    "balance":        float(data["balance"]),
                }
                for _, data in messages
            ]
        }
    except Exception as e:
        return {"snapshots": [], "error": str(e)}


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

    import db
    from db.queries.trades import get_trades as db_get_trades
    pool = db.get_pool()
    trades_data = {"trades": await db_get_trades(pool, days=30)}

    # Calculate per-bot metrics from trades
    bot_metrics = {}
    for bot_key, bot_state in bot_states.items():
        symbol = bot_state.get("symbol", "")
        strategy = bot_state.get("strategy", "")

        # Only count closing fills (realized_pnl != 0) — opening fills have realized_pnl == 0
        symbol_trades = [
            t for t in trades_data["trades"]
            if t["symbol"] == symbol and t["realized_pnl"] != 0
        ]

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

    # Overall portfolio stats — closing fills only
    closing_trades_all = [t for t in trades_data["trades"] if t["realized_pnl"] != 0]
    total_trades_all = len(closing_trades_all)
    winning_trades_all = sum(1 for t in closing_trades_all if t["realized_pnl"] > 0)
    total_pnl_all = sum(t["realized_pnl"] for t in closing_trades_all)

    return {
        "bots": bot_metrics,
        "portfolio": {
            "total_trades": total_trades_all,
            "winning_trades": winning_trades_all,
            "win_rate": round((winning_trades_all / total_trades_all * 100), 1) if total_trades_all > 0 else 0,  # based on closing fills only
            "total_pnl": round(total_pnl_all, 2),
        }
    }


# ── Chat endpoint ─────────────────────────────────────────────────────────────

_LOGS_DIR = Path(__file__).parent.parent / "logs"
_SWEEPS_DIR = Path(__file__).parent.parent / "data" / "sweeps"

_CHAT_TOOLS = [
    {
        "name": "get_account_summary",
        "description": "Retorna saldo USDT, equity total, P&L não realizado e P&L das últimas 24h.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_open_positions",
        "description": "Lista todas as posições abertas com entry price, mark price e P&L não realizado.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_trading_performance",
        "description": "Analisa performance de trading dos últimos N dias. Retorna P&L por símbolo, win rate, identifica melhor e pior ativo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Número de dias (padrão: 7)"},
                "symbol": {"type": "string", "description": "Símbolo específico (ex: AXSUSDT). Omitir para todos."},
            },
            "required": [],
        },
    },
    {
        "name": "get_sweep_results",
        "description": "Mostra melhores configs de backtest (sweep) por símbolo e timeframe.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Símbolo (ex: axsusdt)"},
                "timeframe": {"type": "string", "description": "Timeframe (ex: 5m)"},
                "top_n": {"type": "integer", "description": "Quantas configs retornar (padrão: 10)"},
            },
            "required": [],
        },
    },
    {
        "name": "get_bot_logs",
        "description": "Lê logs recentes dos bots e extrai atividade de trading, erros e mudanças de estado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Símbolo (ex: AXSUSDT)"},
                "hours": {"type": "integer", "description": "Horas atrás (padrão: 24)"},
            },
            "required": [],
        },
    },
    {
        "name": "close_position",
        "description": "Fecha imediatamente uma posição aberta. Cancela todas as ordens (SL/TP) e fecha via ordem a mercado. Use quando o usuário pedir para fechar, encerrar ou sair de uma posição.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Símbolo (ex: AXSUSDT)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "move_stop_to_breakeven",
        "description": "Move o stop loss para o preço de entrada (breakeven / 0a0 / zero a zero). Cancela o SL atual e coloca um novo no entry price.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Símbolo (ex: DOGEUSDT)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "invert_position",
        "description": "Inverte a posição: fecha a atual e abre uma nova no sentido oposto (long→short ou short→long) com mesma quantidade e SL/TP da configuração.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Símbolo (ex: AXSUSDT)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "close_all_positions",
        "description": "Fecha TODAS as posições abertas imediatamente. Use somente quando o usuário pedir explicitamente para fechar tudo.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "query_trades",
        "description": (
            "Consulta a tabela de trades no banco de dados. Use para responder perguntas detalhadas sobre "
            "operações específicas, listar trades de um período ou símbolo, calcular P&L, comissões, "
            "identificar melhores/piores trades, etc. "
            "Colunas disponíveis: id, symbol, side (BUY/SELL), price, qty, realized_pnl, commission, "
            "commission_asset, trade_time (timestamp), order_id, strategy. "
            "Nota: trades de abertura têm realized_pnl=0; trades de fechamento têm realized_pnl≠0. "
            "Em futuros, fechar LONG = side BUY com realized_pnl≠0; fechar SHORT = side SELL com realized_pnl≠0."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Filtrar pelos últimos N dias (padrão: 30)"},
                "symbol": {"type": "string", "description": "Filtrar por símbolo (ex: AXSUSDT). Omitir para todos."},
                "side": {"type": "string", "description": "Filtrar por lado: BUY ou SELL. Omitir para ambos."},
                "only_closing": {"type": "boolean", "description": "Se true, retorna só trades com realized_pnl≠0 (fechamentos). Padrão: false."},
                "limit": {"type": "integer", "description": "Máximo de registros retornados (padrão: 200)"},
                "order_by": {"type": "string", "description": "Coluna para ordenar (padrão: trade_time DESC)"},
            },
            "required": [],
        },
    },
]


async def _chat_query_trades(
    days: int = 30,
    symbol: str | None = None,
    side: str | None = None,
    only_closing: bool = False,
    limit: int = 200,
    order_by: str = "trade_time DESC",
) -> dict:
    import db
    pool = db.get_pool()
    conditions = ["trade_time >= NOW() - make_interval(days => $1)"]
    params: list = [days]
    if symbol:
        params.append(symbol.upper())
        conditions.append(f"symbol = ${len(params)}")
    if side:
        params.append(side.upper())
        conditions.append(f"side = ${len(params)}")
    if only_closing:
        conditions.append("realized_pnl != 0")
    where = " AND ".join(conditions)
    # Whitelist order_by to prevent injection
    allowed_orders = {
        "trade_time DESC", "trade_time ASC",
        "realized_pnl DESC", "realized_pnl ASC",
        "commission DESC", "commission ASC",
    }
    safe_order = order_by if order_by in allowed_orders else "trade_time DESC"
    params.append(min(limit, 500))
    query = f"SELECT symbol, side, price, qty, realized_pnl, commission, commission_asset, trade_time FROM trades WHERE {where} ORDER BY {safe_order} LIMIT ${len(params)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    trades = [dict(r) for r in rows]
    for t in trades:
        if "trade_time" in t and t["trade_time"]:
            t["trade_time"] = t["trade_time"].isoformat()
    total_pnl = sum(t["realized_pnl"] for t in trades)
    total_commission = sum(t["commission"] for t in trades)
    return {
        "count": len(trades),
        "total_realized_pnl": round(total_pnl, 4),
        "total_commission": round(total_commission, 4),
        "net_pnl": round(total_pnl - total_commission, 4),
        "trades": trades,
    }


async def _chat_get_trading_performance(days: int = 7, symbol: str | None = None) -> dict:
    import db
    from db.queries.trades import get_trades as db_get_trades
    pool = db.get_pool()
    data = {"trades": await db_get_trades(pool, symbol=symbol, days=days)}
    by_symbol: dict[str, dict] = {}
    total_pnl = 0.0
    total_trades = 0

    for t in data["trades"]:
        sym = t["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = {
                "symbol": sym, "realized_pnl_usdt": 0.0, "trades": 0,
                "closing_trades": 0, "wins": 0, "commission_usdt": 0.0,
            }
        entry = by_symbol[sym]
        entry["trades"] += 1
        entry["commission_usdt"] += t["commission"]
        if t["realized_pnl"] != 0:
            entry["realized_pnl_usdt"] += t["realized_pnl"]
            entry["closing_trades"] += 1
            if t["realized_pnl"] > 0:
                entry["wins"] += 1
        total_pnl += t["realized_pnl"]
        total_trades += 1

    ranked = []
    for entry in by_symbol.values():
        cl = entry["closing_trades"]
        entry["win_rate_pct"] = round(entry["wins"] / cl * 100, 1) if cl else 0.0
        entry["net_pnl_usdt"] = round(entry["realized_pnl_usdt"] - entry["commission_usdt"], 4)
        entry["realized_pnl_usdt"] = round(entry["realized_pnl_usdt"], 4)
        entry["commission_usdt"] = round(entry["commission_usdt"], 4)
        ranked.append(entry)

    ranked.sort(key=lambda x: x["net_pnl_usdt"])
    return {
        "period_days": days,
        "total_realized_pnl_usdt": round(total_pnl, 4),
        "total_trades": total_trades,
        "symbols_with_activity": len(ranked),
        "worst_asset": ranked[0] if ranked else None,
        "best_asset": ranked[-1] if ranked else None,
        "by_symbol": ranked,
    }


def _chat_get_sweep_results(
    symbol: str | None = None,
    timeframe: str | None = None,
    top_n: int = 10,
) -> dict:
    import pandas as pd

    pattern = "*_sweep.csv"
    if symbol and timeframe:
        pattern = f"{symbol.lower()}_{timeframe}_sweep.csv"
    elif symbol:
        pattern = f"{symbol.lower()}_*_sweep.csv"
    elif timeframe:
        pattern = f"*_{timeframe}_sweep.csv"

    files = list(_SWEEPS_DIR.glob(pattern))
    if not files:
        return {
            "error": "Nenhum arquivo de sweep encontrado.",
            "hint": "Gere os dados com: make sweep-rust SYMBOL=SYMBOL",
        }

    summary = []
    for f in sorted(files):
        try:
            df = pd.read_csv(f)
            best = df.sort_values("return_pct", ascending=False).iloc[0]
            parts = f.stem.replace("_sweep", "").rsplit("_", 1)
            sym = parts[0].upper()
            tf = parts[1] if len(parts) == 2 else "?"
            summary.append({
                "symbol": sym, "timeframe": tf,
                "best_return_pct": float(best.get("return_pct", 0)),
                "strategy": best.get("strategy"),
                "win_rate": best.get("win_rate"),
                "max_dd_pct": best.get("max_dd_pct"),
                "trades": int(best.get("trades", 0)),
            })
        except Exception:
            pass

    summary.sort(key=lambda x: x.get("best_return_pct") or 0)
    return {"files_found": len(files), "summary_ranked_by_return": summary}


def _chat_get_bot_logs(symbol: str | None = None, hours: int = 24) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    pattern = f"bot_{symbol.upper()}_*.log" if symbol else "bot_*_*.log"
    files = sorted(_LOGS_DIR.glob(pattern), reverse=True)
    if not files:
        return {"error": f"Nenhum log encontrado", "pattern": pattern}

    pnl_re = re.compile(r"P&L: \$([+-]?\d+\.\d+) \(([+-]?\d+\.\d+)%\)")
    state_re = re.compile(r"\|\s+(IN_POSITION|SCANNING|COOLDOWN|EOD)\s*$")
    error_re = re.compile(r"(ERROR|Exception|Traceback)", re.IGNORECASE)

    by_symbol: dict[str, dict] = {}
    for log_file in files:
        parts = log_file.stem.split("_")
        if len(parts) < 3:
            continue
        sym, date_str = parts[1], parts[2]
        try:
            log_date = datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError:
            continue
        if log_date < cutoff.date():
            continue
        if sym not in by_symbol:
            by_symbol[sym] = {
                "symbol": sym, "last_pnl_usdt": None, "last_pnl_pct": None,
                "last_state": None, "state_changes": [], "errors": [], "entry_exits": [],
            }
        data = by_symbol[sym]
        try:
            lines = log_file.read_text(errors="replace").splitlines()[-3000:]
        except Exception:
            continue
        for line in lines:
            m = pnl_re.search(line)
            if m:
                data["last_pnl_usdt"] = float(m.group(1))
                data["last_pnl_pct"] = float(m.group(2))
            m = state_re.search(line)
            if m:
                state = m.group(1)
                if state != data["last_state"]:
                    data["state_changes"].append({"state": state, "line": line.strip()[-100:]})
                    data["last_state"] = state
            lower = line.lower()
            if any(kw in lower for kw in ("entry", "opened", "closed", "exit", "filled")):
                data["entry_exits"].append(line.strip()[-120:])
            if error_re.search(line):
                data["errors"].append(line.strip()[-120:])
        data["state_changes"] = data["state_changes"][-10:]
        data["entry_exits"] = data["entry_exits"][-10:]
        data["errors"] = data["errors"][-5:]

    if not by_symbol:
        return {"message": f"Nenhuma atividade nos últimos {hours}h"}
    return {"period_hours": hours, "bots": list(by_symbol.values())}


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


_CHAT_SYSTEM = (
    "Você é um assistente especializado em trading de criptomoedas. "
    "Sempre responda em português brasileiro. "
    "Use as tools disponíveis para consultar dados reais antes de responder. "
    "Seja direto e objetivo. Não apresente menus, listas de funções ou introduções. "
    "Responda apenas o que foi perguntado, de forma concisa. "
    "Você pode executar ações de trading: fechar posições, mover stop para breakeven, "
    "inverter posições e fechar todas as posições. "
    "Sempre confirme o resultado da ação ao usuário após executá-la."
)


class TelegramAlertRequest(BaseModel):
    message: str


@app.post("/api/alert/telegram")
async def send_telegram_alert(req: TelegramAlertRequest):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured"}
    import urllib.parse
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": req.message, "parse_mode": "HTML"}).encode()
    try:
        http_req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(http_req, timeout=5) as resp:
            result = json.loads(resp.read())
        return {"ok": result.get("ok", False)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    provider = os.getenv("CHAT_PROVIDER", "openai").lower().strip()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")

    if provider == "openai":
        if openai_key:
            try:
                return {"response": await _chat_with_openai(req, openai_key)}
            except Exception as e:
                if anthropic_key:
                    try:
                        fallback = await _chat_with_anthropic(req, anthropic_key)
                        return {"response": f"⚠️ OpenAI indisponível ({e}). Resposta via Anthropic:\n\n{fallback}"}
                    except Exception as e2:
                        return {"response": f"⚠️ Falha OpenAI ({e}) e Anthropic ({e2})."}
                return {"response": f"⚠️ Falha ao consultar OpenAI: {e}"}
        if anthropic_key:
            return {"response": await _chat_with_anthropic(req, anthropic_key)}
        return {"response": "⚠️ OPENAI_API_KEY não configurada no .env"}

    if provider not in {"anthropic", "auto"}:
        return {"response": f"⚠️ CHAT_PROVIDER inválido: {provider}. Use: anthropic, openai ou auto"}

    if provider == "anthropic":
        if anthropic_key:
            try:
                return {"response": await _chat_with_anthropic(req, anthropic_key)}
            except Exception as e:
                if openai_key:
                    try:
                        fallback = await _chat_with_openai(req, openai_key)
                        return {"response": f"⚠️ Anthropic indisponível ({e}). Resposta via OpenAI:\n\n{fallback}"}
                    except Exception as e2:
                        return {"response": f"⚠️ Falha Anthropic ({e}) e OpenAI ({e2})."}
                return {"response": f"⚠️ Falha ao consultar Anthropic: {e}"}
        if openai_key:
            return {"response": await _chat_with_openai(req, openai_key)}
        return {"response": "⚠️ ANTHROPIC_API_KEY não configurada no .env"}

    # auto: OpenAI first, fallback to Anthropic
    if openai_key:
        try:
            return {"response": await _chat_with_openai(req, openai_key)}
        except Exception as e:
            if anthropic_key:
                try:
                    fallback = await _chat_with_anthropic(req, anthropic_key)
                    return {"response": f"⚠️ OpenAI indisponível ({e}). Resposta via Anthropic:\n\n{fallback}"}
                except Exception as e2:
                    return {"response": f"⚠️ Falha OpenAI ({e}) e Anthropic ({e2})."}
            return {"response": f"⚠️ Falha ao consultar OpenAI: {e}"}
    if anthropic_key:
        return {"response": await _chat_with_anthropic(req, anthropic_key)}
    return {"response": "⚠️ Configure OPENAI_API_KEY ou ANTHROPIC_API_KEY no .env"}


async def _chat_execute_tool(name: str, inp: dict) -> dict:
    try:
        if name == "get_account_summary":
            return await get_account_summary()
        if name == "get_open_positions":
            return await get_positions()
        if name == "get_trading_performance":
            return await _chat_get_trading_performance(**inp)
        if name == "get_sweep_results":
            return _chat_get_sweep_results(**inp)
        if name == "get_bot_logs":
            return _chat_get_bot_logs(**inp)
        if name == "query_trades":
            return await _chat_query_trades(**inp)
        if name == "close_position":
            _c = await get_client()
            return await api_close_position(**inp)
        if name == "move_stop_to_breakeven":
            return await api_breakeven(**inp)
        if name == "invert_position":
            return await api_invert_position(**inp)
        if name == "close_all_positions":
            return await api_close_all_positions()
        return {"error": f"Tool desconhecida: {name}"}
    except Exception as e:
        return {"error": str(e)}


async def _chat_with_anthropic(req: ChatRequest, api_key: str) -> str:
    import anthropic as _anthropic

    client = _anthropic.Anthropic(api_key=api_key)
    history = req.history[-10:]
    messages = [*history, {"role": "user", "content": req.message}]

    for _ in range(5):
        resp = await asyncio.to_thread(
            lambda msgs=messages: client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=_CHAT_SYSTEM,
                tools=_CHAT_TOOLS,
                messages=msgs,
            )
        )

        if resp.stop_reason == "end_turn":
            return next((b.text for b in resp.content if hasattr(b, "text")), "")

        if resp.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            result = await _chat_execute_tool(block.name, block.input or {})
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                }
            )
        messages.append({"role": "user", "content": tool_results})
    return "Não foi possível gerar uma resposta."


def _openai_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}, "required": []}),
            },
        }
        for t in _CHAT_TOOLS
    ]


async def _chat_with_openai(req: ChatRequest, api_key: str) -> str:
    history = req.history[-10:]
    messages: list[dict] = [{"role": "system", "content": _CHAT_SYSTEM}]
    messages.extend(history)
    messages.append({"role": "user", "content": req.message})

    for _ in range(5):
        payload = {
            "model": os.getenv("OPENAI_CHAT_MODEL", "gpt-5.1"),
            "messages": messages,
            "tools": _openai_tools(),
            "tool_choice": "auto",
        }
        req_http = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        def _do_call():
            try:
                with urllib.request.urlopen(req_http, timeout=20) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                raise RuntimeError(f"HTTP {e.code}: {body[:280] or e.reason}") from e
            except urllib.error.URLError as e:
                raise RuntimeError(f"Conexão OpenAI falhou: {e.reason}") from e
        data = await asyncio.to_thread(_do_call)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            return msg.get("content") or "Não foi possível gerar uma resposta."

        messages.append(
            {
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            }
        )
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            raw_args = fn.get("arguments") or "{}"
            try:
                inp = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except Exception:
                inp = {}
            result = await _chat_execute_tool(name, inp)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "content": json.dumps(result, default=str),
                }
            )

    return "Não foi possível gerar uma resposta."


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
