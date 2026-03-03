"""Pure tool implementations shared by mcp/server.py and trader/api.py chat endpoint."""

import asyncio
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))

from trader.config import (
    BINANCE_API_KEY,
    BINANCE_SECRET_KEY,
    SOCKS_PROXY,
    SYMBOL_CONFIGS,
)

PROJECT_ROOT = Path(__file__).parent.parent
SWEEPS_DIR = PROJECT_ROOT / "data" / "sweeps"
LOGS_DIR = PROJECT_ROOT / "logs"

# ── Binance client (lazy singleton) ──────────────────────────────────────────

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
                raise ValueError("API_KEY / SECRET_KEY não configuradas no .env")
            _client = await asyncio.to_thread(_build_client)
    return _client


# ── DB pool (lazy singleton) ──────────────────────────────────────────────────

_db_pool = None
_db_lock = asyncio.Lock()


async def get_db():
    global _db_pool
    async with _db_lock:
        if _db_pool is None:
            import db as _db
            _db_pool = await _db.init_pool(min_size=1, max_size=5)
    return _db_pool


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── Tool implementations ──────────────────────────────────────────────────────


async def get_account_summary() -> dict:
    """Retorna resumo da conta: saldo USDT, equity total, P&L não realizado e P&L das últimas 24h."""
    client = await get_client()

    balance_resp = await asyncio.to_thread(client.rest_api.futures_account_balance_v3)
    usdt = next((b for b in balance_resp.data() if b.asset == "USDT"), None)
    total_balance = _f(usdt.balance) if usdt else 0.0
    available = _f(usdt.available_balance) if usdt else 0.0

    pos_resp = await asyncio.to_thread(client.rest_api.position_information_v3)
    unrealized_pnl = 0.0
    open_positions = 0
    for p in pos_resp.data():
        if abs(_f(p.position_amt)) > 0:
            open_positions += 1
            unrealized_pnl += _f(p.un_realized_profit)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    try:
        income_resp = await asyncio.to_thread(
            lambda: client.rest_api.income_history(
                start_time=now_ms - 86_400_000, limit=1000
            )
        )
        pnl_24h = sum(
            _f(i.income)
            for i in income_resp.data()
            if i.income_type in ("REALIZED_PNL", "FUNDING_FEE")
        )
    except Exception:
        pnl_24h = 0.0

    total_equity = total_balance + unrealized_pnl
    return {
        "total_balance_usdt": round(total_balance, 2),
        "available_balance_usdt": round(available, 2),
        "total_equity_usdt": round(total_equity, 2),
        "unrealized_pnl_usdt": round(unrealized_pnl, 2),
        "pnl_24h_usdt": round(pnl_24h, 2),
        "open_positions": open_positions,
    }


async def get_open_positions() -> dict:
    """Lista todas as posições abertas com entry price, mark price e P&L não realizado."""
    client = await get_client()
    resp = await asyncio.to_thread(client.rest_api.position_information_v3)

    positions = []
    for p in resp.data():
        amt = _f(p.position_amt)
        if abs(amt) == 0:
            continue
        entry = _f(p.entry_price)
        mark = _f(getattr(p, "mark_price", 0))
        upnl = _f(p.un_realized_profit)
        leverage = int(_f(getattr(p, "leverage", 1)))
        pct = ((mark - entry) / entry * 100 * (-1 if amt < 0 else 1)) if entry else 0
        positions.append({
            "symbol": p.symbol,
            "side": "LONG" if amt > 0 else "SHORT",
            "qty": abs(amt),
            "entry_price": entry,
            "mark_price": mark,
            "unrealized_pnl_usdt": round(upnl, 4),
            "pnl_pct": round(pct, 2),
            "leverage": leverage,
        })

    positions.sort(key=lambda x: x["unrealized_pnl_usdt"])
    return {"positions": positions, "count": len(positions)}


async def get_trading_performance(days: int = 7, symbol: str | None = None) -> dict:
    """Analisa performance de trading dos últimos N dias via banco de dados.

    Retorna P&L realizado por símbolo, total de trades, win rate e comissões.
    Ordena por P&L para identificar melhor e pior ativo.
    """
    pool = await get_db()
    async with pool.acquire() as conn:
        q = """
            SELECT symbol, side, realized_pnl, commission
            FROM trades
            WHERE trade_time >= NOW() - make_interval(days => $1)
        """
        params = [days]
        if symbol:
            q += " AND symbol = $2"
            params.append(symbol.upper())
        rows = await conn.fetch(q, *params)

    by_symbol: dict[str, dict] = {}
    total_pnl = 0.0

    for r in rows:
        sym = r["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = {"symbol": sym, "realized_pnl": 0.0, "commission": 0.0,
                               "trades": 0, "closing": 0, "wins": 0}
        e = by_symbol[sym]
        e["trades"] += 1
        e["commission"] += float(r["commission"])
        pnl = float(r["realized_pnl"])
        if pnl != 0:
            e["realized_pnl"] += pnl
            e["closing"] += 1
            if pnl > 0:
                e["wins"] += 1
        total_pnl += pnl

    ranked = []
    for e in by_symbol.values():
        cl = e["closing"]
        ranked.append({
            "symbol": e["symbol"],
            "realized_pnl_usdt": round(e["realized_pnl"], 4),
            "commission_usdt": round(e["commission"], 4),
            "net_pnl_usdt": round(e["realized_pnl"] - e["commission"], 4),
            "trades": e["trades"],
            "closing_trades": cl,
            "wins": e["wins"],
            "losses": cl - e["wins"],
            "win_rate_pct": round(e["wins"] / cl * 100, 1) if cl else 0.0,
        })

    ranked.sort(key=lambda x: x["net_pnl_usdt"])
    total_commission = sum(e["commission_usdt"] for e in ranked)
    return {
        "period_days": days,
        "total_realized_pnl_usdt": round(total_pnl, 4),
        "total_commission_usdt": round(total_commission, 4),
        "total_net_pnl_usdt": round(total_pnl - total_commission, 4),
        "total_trades": sum(e["trades"] for e in ranked),
        "symbols_with_activity": len(ranked),
        "worst_asset": ranked[0] if ranked else None,
        "best_asset": ranked[-1] if ranked else None,
        "by_symbol": ranked,
    }


async def query_trades(
    days: int = 30,
    symbol: str | None = None,
    side: str | None = None,
    only_closing: bool = False,
    limit: int = 200,
    order_by: str = "trade_time DESC",
) -> dict:
    """Consulta direta à tabela de trades. Use para listar operações específicas,
    calcular P&L detalhado, investigar prejuízos, etc.

    Colunas: symbol, side (BUY/SELL), price, qty, realized_pnl, commission, trade_time.
    Em futuros: fechar LONG = BUY com realized_pnl≠0; fechar SHORT = SELL com realized_pnl≠0.
    """
    pool = await get_db()
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

    allowed_orders = {
        "trade_time DESC", "trade_time ASC",
        "realized_pnl DESC", "realized_pnl ASC",
        "commission DESC", "commission ASC",
    }
    safe_order = order_by if order_by in allowed_orders else "trade_time DESC"
    params.append(min(limit, 500))

    q = f"""SELECT symbol, side, price, qty, realized_pnl, commission, commission_asset, trade_time
            FROM trades WHERE {' AND '.join(conditions)}
            ORDER BY {safe_order} LIMIT ${len(params)}"""

    async with (await get_db()).acquire() as conn:
        rows = await conn.fetch(q, *params)

    trades = []
    for r in rows:
        t = dict(r)
        t["trade_time"] = t["trade_time"].isoformat()
        t["realized_pnl"] = float(t["realized_pnl"])
        t["commission"] = float(t["commission"])
        t["price"] = float(t["price"])
        t["qty"] = float(t["qty"])
        trades.append(t)

    total_pnl = sum(t["realized_pnl"] for t in trades)
    total_commission = sum(t["commission"] for t in trades)
    return {
        "count": len(trades),
        "total_realized_pnl": round(total_pnl, 4),
        "total_commission": round(total_commission, 4),
        "net_pnl": round(total_pnl - total_commission, 4),
        "trades": trades,
    }


async def get_daily_performance(days: int = 30, symbol: str | None = None) -> dict:
    """P&L realizado agrupado por dia. Útil para identificar dias bons/ruins."""
    pool = await get_db()
    params: list = [days]
    sym_filter = ""
    if symbol:
        params.append(symbol.upper())
        sym_filter = f"AND symbol = ${len(params)}"

    q = f"""
        SELECT
            DATE(trade_time AT TIME ZONE 'UTC') AS day,
            SUM(realized_pnl) AS gross_pnl,
            SUM(commission) AS commission,
            SUM(realized_pnl) - SUM(commission) AS net_pnl,
            COUNT(*) FILTER (WHERE realized_pnl != 0) AS closing_trades,
            COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
            COUNT(*) FILTER (WHERE realized_pnl < 0) AS losses
        FROM trades
        WHERE trade_time >= NOW() - make_interval(days => $1)
          AND realized_pnl != 0
          {sym_filter}
        GROUP BY day
        ORDER BY day DESC
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(q, *params)

    daily = []
    for r in rows:
        gross = float(r["gross_pnl"])
        comm = float(r["commission"])
        net = float(r["net_pnl"])
        cl = r["closing_trades"]
        daily.append({
            "date": r["day"].isoformat(),
            "gross_pnl": round(gross, 4),
            "commission": round(comm, 4),
            "net_pnl": round(net, 4),
            "closing_trades": cl,
            "wins": r["wins"],
            "losses": r["losses"],
            "win_rate_pct": round(r["wins"] / cl * 100, 1) if cl else 0.0,
        })

    total_net = sum(d["net_pnl"] for d in daily)
    best = max(daily, key=lambda x: x["net_pnl"], default=None)
    worst = min(daily, key=lambda x: x["net_pnl"], default=None)
    return {
        "period_days": days,
        "days_with_trades": len(daily),
        "total_net_pnl": round(total_net, 4),
        "best_day": best,
        "worst_day": worst,
        "daily": daily,
    }


async def get_commission_report(days: int = 30) -> dict:
    """Relatório de comissões pagas por símbolo e por asset."""
    pool = await get_db()
    async with pool.acquire() as conn:
        by_symbol = await conn.fetch("""
            SELECT symbol, SUM(commission) AS total, COUNT(*) AS trades
            FROM trades
            WHERE trade_time >= NOW() - make_interval(days => $1)
            GROUP BY symbol ORDER BY total DESC
        """, days)

        by_asset = await conn.fetch("""
            SELECT commission_asset, SUM(commission) AS total, COUNT(*) AS trades
            FROM trades
            WHERE trade_time >= NOW() - make_interval(days => $1)
            GROUP BY commission_asset ORDER BY total DESC
        """, days)

        total_row = await conn.fetchrow("""
            SELECT SUM(commission) AS total FROM trades
            WHERE trade_time >= NOW() - make_interval(days => $1)
        """, days)

    return {
        "period_days": days,
        "total_commission": round(float(total_row["total"] or 0), 6),
        "by_symbol": [{"symbol": r["symbol"], "commission": round(float(r["total"]), 6), "trades": r["trades"]} for r in by_symbol],
        "by_asset": [{"asset": r["commission_asset"], "commission": round(float(r["total"]), 6), "trades": r["trades"]} for r in by_asset],
    }


async def get_portfolio_stats(days: int = 30) -> dict:
    """Estatísticas avançadas do portfólio: profit factor, expectancy, max drawdown,
    Sharpe ratio, maior win/loss, sequência de vitórias/derrotas."""
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT realized_pnl, commission, trade_time
            FROM trades
            WHERE trade_time >= NOW() - make_interval(days => $1)
              AND realized_pnl != 0
            ORDER BY trade_time ASC
        """, days)

    if not rows:
        return {"error": "Sem trades no período"}

    pnls = [float(r["realized_pnl"]) for r in rows]
    commissions = [float(r["commission"]) for r in rows]
    net_pnls = [p - c for p, c in zip(pnls, commissions)]

    winners = [p for p in net_pnls if p > 0]
    losers = [p for p in net_pnls if p < 0]

    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    total_net = sum(net_pnls)
    total_trades = len(net_pnls)
    win_rate = len(winners) / total_trades * 100

    avg_win = gross_profit / len(winners) if winners else 0
    avg_loss = gross_loss / len(losers) if losers else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    expectancy = (win_rate / 100) * avg_win - ((100 - win_rate) / 100) * avg_loss

    # Max drawdown
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in net_pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Win/loss streaks
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    for p in net_pnls:
        if p > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        max_win_streak = max(max_win_streak, cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)

    # Simplified Sharpe (daily returns)
    import math
    avg_ret = sum(net_pnls) / total_trades
    variance = sum((p - avg_ret) ** 2 for p in net_pnls) / total_trades
    std_dev = math.sqrt(variance)
    sharpe = (avg_ret / std_dev) * math.sqrt(252) if std_dev > 0 else 0

    return {
        "period_days": days,
        "total_trades": total_trades,
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "win_rate_pct": round(win_rate, 2),
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "total_net_pnl": round(total_net, 4),
        "total_commission": round(sum(commissions), 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "largest_win": round(max(winners), 4) if winners else 0,
        "largest_loss": round(min(losers), 4) if losers else 0,
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else "∞",
        "expectancy_per_trade": round(expectancy, 4),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
    }


async def get_trade_streaks(days: int = 30, symbol: str | None = None) -> dict:
    """Sequências de wins/losses consecutivos por símbolo."""
    pool = await get_db()
    params: list = [days]
    sym_filter = ""
    if symbol:
        params.append(symbol.upper())
        sym_filter = f"AND symbol = ${len(params)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT symbol, realized_pnl, trade_time
            FROM trades
            WHERE trade_time >= NOW() - make_interval(days => $1)
              AND realized_pnl != 0
              {sym_filter}
            ORDER BY symbol, trade_time ASC
        """, *params)

    by_sym: dict[str, list] = {}
    for r in rows:
        sym = r["symbol"]
        if sym not in by_sym:
            by_sym[sym] = []
        by_sym[sym].append(float(r["realized_pnl"]))

    result = []
    for sym, pnls in by_sym.items():
        max_win = max_loss = cur_win = cur_loss = 0
        current_streak = 0
        current_type = None
        for p in pnls:
            if p > 0:
                cur_win += 1; cur_loss = 0
                current_streak = cur_win; current_type = "WIN"
            else:
                cur_loss += 1; cur_win = 0
                current_streak = cur_loss; current_type = "LOSS"
            max_win = max(max_win, cur_win)
            max_loss = max(max_loss, cur_loss)
        result.append({
            "symbol": sym,
            "trades": len(pnls),
            "max_win_streak": max_win,
            "max_loss_streak": max_loss,
            "current_streak": current_streak,
            "current_streak_type": current_type,
        })

    result.sort(key=lambda x: x["max_loss_streak"], reverse=True)
    return {"period_days": days, "symbols": result}


def get_bot_configs() -> dict:
    """Retorna as configurações de todos os bots ativos: leverage, pos_size, TP, SL, etc."""
    configs = []
    for sym, cfg in SYMBOL_CONFIGS.items():
        configs.append({
            "symbol": sym,
            "tp_pct": getattr(cfg, "tp_pct", None),
            "sl_pct": getattr(cfg, "sl_pct", None),
            "pos_size_pct": getattr(cfg, "pos_size_pct", None),
            "min_bars": getattr(cfg, "min_bars", None),
            "confirm_bars": getattr(cfg, "confirm_bars", None),
            "vwap_prox": getattr(cfg, "vwap_prox", None),
            "interval": getattr(cfg, "interval", None),
            "vol_filter": getattr(cfg, "vol_filter", None),
            "max_trades_per_day": getattr(cfg, "max_trades_per_day", None),
        })
    return {"total_symbols": len(configs), "configs": configs}


async def get_bot_states() -> dict:
    """Estado atual de todos os bots via Redis: SCANNING, IN_POSITION, COOLDOWN."""
    try:
        import redis
        import json
        r = redis.Redis(
            host=__import__("os").environ.get("REDIS_HOST", "localhost"),
            port=int(__import__("os").environ.get("REDIS_PORT", 6379)),
            decode_responses=True,
        )
        raw = r.get("bot:states")
        if not raw:
            return {"error": "bot:states not found in Redis — bots may not be running"}
        states = json.loads(raw)
        in_position = [k for k, v in states.items() if v.get("state") == "IN_POSITION"]
        scanning = [k for k, v in states.items() if v.get("state") == "SCANNING"]
        cooldown = [k for k, v in states.items() if v.get("state") == "COOLDOWN"]
        return {
            "total_bots": len(states),
            "in_position": len(in_position),
            "scanning": len(scanning),
            "cooldown": len(cooldown),
            "in_position_list": in_position,
            "states": states,
        }
    except Exception as e:
        return {"error": str(e)}


def get_running_processes() -> dict:
    """Lista os processos de bot atualmente rodando no sistema."""
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True
        )
        lines = result.stdout.splitlines()
        bot_lines = [l for l in lines if "python -m trader" in l and "grep" not in l]
        bots = []
        for line in bot_lines:
            parts = line.split()
            pid = parts[1] if len(parts) > 1 else "?"
            cmd = " ".join(parts[10:]) if len(parts) > 10 else line
            bots.append({"pid": pid, "command": cmd})
        return {"count": len(bots), "processes": bots}
    except Exception as e:
        return {"error": str(e)}


async def get_market_prices() -> dict:
    """Preços atuais de mercado para todos os símbolos monitorados."""
    client = await get_client()
    prices = {}
    for sym in SYMBOL_CONFIGS:
        try:
            resp = await asyncio.to_thread(
                lambda s=sym: client.rest_api.symbol_price_ticker_v2(symbol=s)
            )
            data = resp.data()
            item = data[0] if isinstance(data, list) else data
            prices[sym] = round(_f(item.price), 8)
        except Exception:
            prices[sym] = None
    return {"prices": prices, "count": len(prices)}


async def get_klines(symbol: str, interval: str = "5m", limit: int = 50) -> dict:
    """Candles (OHLCV) de um símbolo. intervals: 1m, 5m, 15m, 30m, 1h, 4h, 1d."""
    client = await get_client()
    resp = await asyncio.to_thread(
        lambda: client.rest_api.klines(
            symbol=symbol.upper(), interval=interval, limit=limit
        )
    )
    raw = resp.data()
    candles = []
    for c in raw:
        candles.append({
            "time": datetime.fromtimestamp(int(c[0]) / 1000, tz=timezone.utc).isoformat(),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
        })
    if candles:
        last = candles[-1]
        first = candles[0]
        change_pct = (last["close"] - first["open"]) / first["open"] * 100 if first["open"] else 0
    else:
        change_pct = 0
    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "candles": candles,
        "count": len(candles),
        "latest_price": candles[-1]["close"] if candles else None,
        "change_pct": round(change_pct, 2),
    }


def get_sweep_results(
    symbol: str | None = None,
    timeframe: str | None = None,
    top_n: int = 10,
) -> dict:
    """Mostra os melhores resultados de backtest (sweep) por símbolo e timeframe."""
    pattern = "*_sweep.csv"
    if symbol and timeframe:
        pattern = f"{symbol.lower()}_{timeframe}_sweep.csv"
    elif symbol:
        pattern = f"{symbol.lower()}_*_sweep.csv"
    elif timeframe:
        pattern = f"*_{timeframe}_sweep.csv"

    files = list(SWEEPS_DIR.glob(pattern))
    if not files:
        return {
            "error": "Nenhum arquivo de sweep encontrado.",
            "hint": "Gere os dados com: make sweep-rust SYMBOL=SYMBOL",
            "path_checked": str(SWEEPS_DIR),
        }

    results = []
    for f in sorted(files):
        try:
            df = pd.read_csv(f)
            df = df.sort_values("return_pct", ascending=False).head(top_n)
            name_parts = f.stem.replace("_sweep", "").rsplit("_", 1)
            sym = name_parts[0].upper() if len(name_parts) >= 1 else f.stem
            tf = name_parts[1] if len(name_parts) == 2 else "?"
            results.append({
                "symbol": sym,
                "timeframe": tf,
                "top_configs": df.to_dict(orient="records"),
            })
        except Exception as e:
            results.append({"file": f.name, "error": str(e)})

    summary = []
    for r in results:
        if "top_configs" in r and r["top_configs"]:
            best = r["top_configs"][0]
            summary.append({
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "best_return_pct": best.get("return_pct"),
                "strategy": best.get("strategy"),
                "win_rate": best.get("win_rate"),
                "max_dd_pct": best.get("max_dd_pct"),
                "trades": best.get("trades"),
            })

    summary.sort(key=lambda x: x.get("best_return_pct") or 0)
    return {
        "files_found": len(files),
        "summary_ranked_by_return": summary,
        "details": results,
    }


def get_bot_logs(symbol: str | None = None, hours: int = 24) -> dict:
    """Lê logs recentes dos bots e extrai atividade de trading."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    cutoff_date = cutoff.date()

    pattern = f"bot_{symbol.upper()}_*.log" if symbol else "bot_*_*.log"
    files = sorted(LOGS_DIR.glob(pattern), reverse=True)
    if not files:
        return {"error": f"Nenhum log encontrado em {LOGS_DIR}", "pattern": pattern}

    pnl_re = re.compile(r"P&L: \$([+-]?\d+\.\d+) \(([+-]?\d+\.\d+)%\)")
    state_re = re.compile(r"\|\s+(IN_POSITION|SCANNING|COOLDOWN|EOD)\s*$")
    error_re = re.compile(r"(ERROR|Exception|Traceback|error)", re.IGNORECASE)

    by_symbol: dict[str, dict] = {}
    MAX_LINES_PER_FILE = 5000

    for log_file in files:
        parts = log_file.stem.split("_")
        if len(parts) < 3:
            continue
        sym = parts[1]
        date_str = parts[2]
        try:
            log_date = datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError:
            continue

        if log_date < cutoff_date:
            continue

        if sym not in by_symbol:
            by_symbol[sym] = {
                "symbol": sym,
                "files_read": [],
                "last_pnl_usdt": None,
                "last_pnl_pct": None,
                "last_state": None,
                "state_changes": [],
                "errors": [],
                "entry_exits": [],
            }

        data = by_symbol[sym]
        data["files_read"].append(log_file.name)

        try:
            lines = log_file.read_text(errors="replace").splitlines()[-MAX_LINES_PER_FILE:]
        except Exception:
            continue

        for line in lines:
            ts_match = re.match(r"^(\d{2}:\d{2}:\d{2})", line)
            if ts_match:
                t_str = ts_match.group(1)
                try:
                    line_dt = datetime.combine(
                        log_date,
                        datetime.strptime(t_str, "%H:%M:%S").time(),
                        tzinfo=timezone.utc,
                    )
                    if line_dt < cutoff:
                        continue
                except ValueError:
                    pass

            m = pnl_re.search(line)
            if m:
                data["last_pnl_usdt"] = float(m.group(1))
                data["last_pnl_pct"] = float(m.group(2))

            m = state_re.search(line)
            if m:
                state = m.group(1)
                if state != data["last_state"]:
                    data["state_changes"].append({"state": state, "line": line.strip()[-120:]})
                    data["last_state"] = state

            lower = line.lower()
            if any(kw in lower for kw in ("entry", "opened", "closed", "exit", "filled", "stop", "take profit")):
                data["entry_exits"].append(line.strip()[-150:])

            if error_re.search(line):
                data["errors"].append(line.strip()[-150:])

        data["state_changes"] = data["state_changes"][-20:]
        data["entry_exits"] = data["entry_exits"][-20:]
        data["errors"] = data["errors"][-10:]

    if not by_symbol:
        return {
            "message": f"Nenhuma atividade nos últimos {hours}h",
            "symbols_checked": symbol or "todos",
        }

    return {
        "period_hours": hours,
        "symbols_active": len(by_symbol),
        "bots": list(by_symbol.values()),
    }
