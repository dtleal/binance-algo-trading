"""Pure tool implementations shared by mcp/server.py and trader/api.py chat endpoint."""

import asyncio
import re
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
    """Analisa performance de trading dos últimos N dias.

    Retorna P&L realizado por símbolo, total de trades, win rate e comissões.
    Ordena por P&L para identificar melhor e pior ativo.
    """
    client = await get_client()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_ms = now_ms - days * 86_400_000

    symbols = [symbol.upper()] if symbol else list(SYMBOL_CONFIGS.keys())

    by_symbol: dict[str, dict] = {}
    total_pnl = 0.0
    total_trades = 0

    for sym in symbols:
        try:
            resp = await asyncio.to_thread(
                lambda s=sym: client.rest_api.account_trade_list(symbol=s, limit=500)
            )
            trades = [t for t in resp.data() if int(t.time) >= cutoff_ms]
            if not trades:
                continue

            pnl = sum(_f(t.realized_pnl) for t in trades)
            commission = sum(_f(t.commission) for t in trades)
            closing = [t for t in trades if _f(t.realized_pnl) != 0]
            wins = sum(1 for t in closing if _f(t.realized_pnl) > 0)
            win_rate = round(wins / len(closing) * 100, 1) if closing else 0.0

            by_symbol[sym] = {
                "symbol": sym,
                "realized_pnl_usdt": round(pnl, 4),
                "trades": len(trades),
                "closing_trades": len(closing),
                "wins": wins,
                "win_rate_pct": win_rate,
                "commission_usdt": round(commission, 4),
                "net_pnl_usdt": round(pnl - commission, 4),
            }
            total_pnl += pnl
            total_trades += len(trades)
        except Exception:
            pass

    ranked = sorted(by_symbol.values(), key=lambda x: x["net_pnl_usdt"])
    return {
        "period_days": days,
        "total_realized_pnl_usdt": round(total_pnl, 4),
        "total_trades": total_trades,
        "symbols_with_activity": len(by_symbol),
        "worst_asset": ranked[0] if ranked else None,
        "best_asset": ranked[-1] if ranked else None,
        "by_symbol": ranked,
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
