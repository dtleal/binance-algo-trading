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


@mcp.tool()
async def get_account_summary() -> dict:
    """Retorna resumo da conta: saldo USDT, equity total, P&L não realizado e P&L das últimas 24h."""
    return await tools.get_account_summary()


@mcp.tool()
async def get_open_positions() -> dict:
    """Lista todas as posições abertas com entry price, mark price e P&L não realizado."""
    return await tools.get_open_positions()


@mcp.tool()
async def get_trading_performance(days: int = 7, symbol: str | None = None) -> dict:
    """Analisa performance de trading dos últimos N dias.

    Retorna P&L realizado por símbolo, total de trades, win rate e comissões.
    Ordena por P&L para identificar melhor e pior ativo.

    Args:
        days: Número de dias para análise (padrão: 7)
        symbol: Filtrar por símbolo específico (ex: "AXSUSDT"). None = todos.
    """
    return await tools.get_trading_performance(days=days, symbol=symbol)


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


@mcp.tool()
def get_bot_logs(symbol: str | None = None, hours: int = 24) -> dict:
    """Lê logs recentes dos bots e extrai atividade de trading.

    Args:
        symbol: Filtrar por símbolo (ex: "AXSUSDT"). None = todos.
        hours: Quantas horas atrás consultar (padrão: 24)
    """
    return tools.get_bot_logs(symbol=symbol, hours=hours)


if __name__ == "__main__":
    mcp.run()
