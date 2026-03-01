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
    """Background task: snapshot account equity every 5 minutes into Redis."""
    import os
    import redis.asyncio as aioredis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    r = await aioredis.from_url(redis_url, decode_responses=True)

    while True:
        try:
            summary = await get_account_summary()
            ts = int(datetime.now(timezone.utc).timestamp() * 1000)
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


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_equity_snapshot_loop())

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

    window_ms = 7 * 86_400_000  # Binance API max window per request

    # Build list of (symbol, win_start, win_end) tasks covering the full range
    tasks: list[tuple[str, int, int]] = []
    for sym in symbols:
        win_start = cutoff_ms
        while win_start < now_ms:
            tasks.append((sym, win_start, min(win_start + window_ms, now_ms)))
            win_start = min(win_start + window_ms, now_ms) + 1

    async def _fetch_window(sym: str, st: int, et: int) -> list[dict]:
        try:
            resp = await asyncio.to_thread(
                lambda s=sym, a=st, b=et: client.rest_api.account_trade_list(
                    symbol=s, startTime=a, endTime=b, limit=1000
                )
            )
            return [
                {
                    "symbol":           t.symbol,
                    "side":             t.side,
                    "price":            _safe_float(t.price),
                    "qty":              _safe_float(t.qty),
                    "realized_pnl":     _safe_float(t.realized_pnl),
                    "commission":       _safe_float(t.commission),
                    "commission_asset": t.commission_asset,
                    "time":             int(t.time),
                    "order_id":         t.order_id,
                    "buyer":            t.buyer,
                }
                for t in resp.data()
            ]
        except Exception:
            return []

    results = await asyncio.gather(*[_fetch_window(s, st, et) for s, st, et in tasks])
    all_trades: list[dict] = [t for batch in results for t in batch]

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


@app.get("/api/equity_history")
async def get_equity_history(days: int = 7):
    """Return equity snapshots (5-min interval) stored in Redis Stream."""
    import os
    import redis.asyncio as aioredis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    r = await aioredis.from_url(redis_url, decode_responses=True)

    cutoff_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - days * 86_400_000
    min_id = f"{cutoff_ms}-0"
    try:
        messages = await r.xrange(_EQUITY_STREAM, min=min_id)
        snapshots = [
            {
                "time":           int(data["time"]),
                "equity":         float(data["equity"]),
                "unrealized_pnl": float(data["unrealized_pnl"]),
                "balance":        float(data["balance"]),
            }
            for _, data in messages
        ]
        return {"snapshots": snapshots}
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
    trades_data = await get_trades(days=30)

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
]


async def _chat_get_trading_performance(days: int = 7, symbol: str | None = None) -> dict:
    data = await get_trades(symbol=symbol, days=days)
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
    "Seja direto e objetivo nas respostas."
)


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    import anthropic as _anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"response": "⚠️ ANTHROPIC_API_KEY não configurada no .env"}

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
            text = next((b.text for b in resp.content if hasattr(b, "text")), "")
            return {"response": text}

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                try:
                    inp = block.input
                    if block.name == "get_account_summary":
                        result = await get_account_summary()
                    elif block.name == "get_open_positions":
                        result = await get_positions()
                    elif block.name == "get_trading_performance":
                        result = await _chat_get_trading_performance(**inp)
                    elif block.name == "get_sweep_results":
                        result = _chat_get_sweep_results(**inp)
                    elif block.name == "get_bot_logs":
                        result = _chat_get_bot_logs(**inp)
                    else:
                        result = {"error": f"Tool desconhecida: {block.name}"}
                except Exception as e:
                    result = {"error": str(e)}

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return {"response": "Não foi possível gerar uma resposta."}


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
