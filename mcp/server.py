"""MCP server for Binance Trader.

Expõe tools para o Claude Code consultar dados de trading em linguagem natural.

Registrar em ~/.claude/settings.json:
    {
      "mcpServers": {
        "binance-trader": {
          "command": "poetry",
          "args": ["run", "python", "mcp/server.py"],
          "cwd": "/Users/diegoleal/binance-trader"
        }
      }
    }
"""

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import tools

mcp = FastMCP("binance-trader")


# ── Conta & Posições ──────────────────────────────────────────────────────────

@mcp.tool()
async def get_account_summary() -> dict:
    """Retorna resumo da conta: saldo USDT, equity total, P&L não realizado e P&L das últimas 24h."""
    return await tools.get_account_summary()


@mcp.tool()
async def get_open_positions() -> dict:
    """Lista todas as posições abertas com entry price, mark price, P&L não realizado e leverage."""
    return await tools.get_open_positions()


# ── Performance & Trades ──────────────────────────────────────────────────────

@mcp.tool()
async def get_trading_performance(days: int = 7, symbol: str | None = None) -> dict:
    """Analisa performance de trading dos últimos N dias por símbolo.

    Retorna P&L realizado, win rate, comissões e P&L líquido.
    Ordena do pior ao melhor para identificar ativos problemáticos.

    Args:
        days: Número de dias para análise (padrão: 7)
        symbol: Filtrar por símbolo específico (ex: "AXSUSDT"). None = todos.
    """
    return await tools.get_trading_performance(days=days, symbol=symbol)


@mcp.tool()
async def query_trades(
    days: int = 30,
    symbol: str | None = None,
    side: str | None = None,
    only_closing: bool = False,
    limit: int = 200,
    order_by: str = "trade_time DESC",
) -> dict:
    """Consulta direta à tabela de trades no banco de dados.

    Use para: listar operações específicas, investigar prejuízos, calcular totais,
    ver detalhes de um símbolo, encontrar maiores wins/losses, etc.

    Em futuros: fechar LONG = BUY com realized_pnl≠0; fechar SHORT = SELL com realized_pnl≠0.

    Args:
        days: Últimos N dias (padrão: 30)
        symbol: Filtrar por símbolo (ex: "GALAUSDT")
        side: Filtrar por lado "BUY" ou "SELL"
        only_closing: Se true, apenas trades com realized_pnl≠0 (fechamentos)
        limit: Máximo de registros (padrão: 200, máx: 500)
        order_by: Ordenação — "trade_time DESC/ASC", "realized_pnl DESC/ASC"
    """
    return await tools.query_trades(
        days=days, symbol=symbol, side=side,
        only_closing=only_closing, limit=limit, order_by=order_by,
    )


@mcp.tool()
async def get_daily_performance(days: int = 30, symbol: str | None = None) -> dict:
    """P&L realizado agrupado por dia. Identifica os melhores e piores dias de trading.

    Args:
        days: Últimos N dias (padrão: 30)
        symbol: Filtrar por símbolo específico
    """
    return await tools.get_daily_performance(days=days, symbol=symbol)


@mcp.tool()
async def get_commission_report(days: int = 30) -> dict:
    """Relatório completo de comissões: total pago, breakdown por símbolo e por asset.

    Args:
        days: Últimos N dias (padrão: 30)
    """
    return await tools.get_commission_report(days=days)


@mcp.tool()
async def get_portfolio_stats(days: int = 30) -> dict:
    """Estatísticas avançadas do portfólio:
    profit factor, expectancy por trade, max drawdown, Sharpe ratio,
    maior win/loss, sequência máxima de vitórias/derrotas.

    Args:
        days: Últimos N dias (padrão: 30)
    """
    return await tools.get_portfolio_stats(days=days)


@mcp.tool()
async def get_trade_streaks(days: int = 30, symbol: str | None = None) -> dict:
    """Sequências consecutivas de wins e losses por símbolo.
    Útil para identificar ativos em série de perdas.

    Args:
        days: Últimos N dias (padrão: 30)
        symbol: Filtrar por símbolo específico
    """
    return await tools.get_trade_streaks(days=days, symbol=symbol)


# ── Configuração dos Bots ─────────────────────────────────────────────────────

@mcp.tool()
def get_bot_configs() -> dict:
    """Retorna as configurações de todos os bots ativos:
    TP%, SL%, position size, min_bars, confirm_bars, vwap_prox, interval, etc."""
    return tools.get_bot_configs()


# ── Estado do Sistema ─────────────────────────────────────────────────────────

@mcp.tool()
async def get_bot_states() -> dict:
    """Estado atual de todos os bots via Redis: SCANNING, IN_POSITION, COOLDOWN.
    Mostra quais bots estão em posição agora."""
    return await tools.get_bot_states()


@mcp.tool()
def get_running_processes() -> dict:
    """Lista os processos de bot Python atualmente rodando no sistema (via ps aux)."""
    return tools.get_running_processes()


# ── Mercado & Dados Históricos ────────────────────────────────────────────────

@mcp.tool()
async def get_market_prices() -> dict:
    """Preços atuais de mercado para todos os símbolos monitorados pelos bots."""
    return await tools.get_market_prices()


@mcp.tool()
async def get_klines(symbol: str, interval: str = "5m", limit: int = 50) -> dict:
    """Candles OHLCV de um símbolo para análise técnica.

    Args:
        symbol: Símbolo (ex: "BTCUSDT")
        interval: Timeframe — 1m, 5m, 15m, 30m, 1h, 4h, 1d (padrão: 5m)
        limit: Número de candles (padrão: 50, máx: 500)
    """
    return await tools.get_klines(symbol=symbol, interval=interval, limit=limit)


# ── Backtest ──────────────────────────────────────────────────────────────────

@mcp.tool()
def get_sweep_results(
    symbol: str | None = None,
    timeframe: str | None = None,
    top_n: int = 10,
) -> dict:
    """Mostra os melhores resultados de backtest (sweep) por símbolo e timeframe.

    Args:
        symbol: Filtrar por símbolo (ex: "axsusdt"). None = todos.
        timeframe: Filtrar por timeframe (ex: "5m"). None = todos.
        top_n: Quantas configs retornar por arquivo (padrão: 10)
    """
    return tools.get_sweep_results(symbol=symbol, timeframe=timeframe, top_n=top_n)


# ── Logs ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_bot_logs(symbol: str | None = None, hours: int = 24) -> dict:
    """Lê logs recentes dos bots: entradas/saídas, mudanças de estado, erros.

    Args:
        symbol: Filtrar por símbolo (ex: "AXSUSDT"). None = todos.
        hours: Quantas horas atrás consultar (padrão: 24)
    """
    return tools.get_bot_logs(symbol=symbol, hours=hours)


if __name__ == "__main__":
    mcp.run()
